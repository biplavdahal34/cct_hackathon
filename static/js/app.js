/* BEAT — one small script for every page. Each block checks for its elements
   so the file is safe to load everywhere. The pre-paint theme snippet stays
   inline in the layouts because it must run before first render. */
(function () {
  "use strict";
  var html = document.documentElement;

  /* theme toggle */
  var toggles = document.querySelectorAll("[data-theme-toggle]");
  for (var i = 0; i < toggles.length; i++) {
    toggles[i].addEventListener("click", function () {
      html.dataset.theme = html.dataset.theme === "dark" ? "light" : "dark";
      try { localStorage.setItem("theme", html.dataset.theme); } catch (e) {}
    });
  }

  /* dismissable flash messages */
  var xs = document.querySelectorAll(".flash .x");
  for (var j = 0; j < xs.length; j++) {
    xs[j].addEventListener("click", function () { this.parentNode.remove(); });
  }

  /* home: analyze form → fold the page away, show status + elapsed time */
  var form = document.getElementById("analyzeForm");
  if (form) {
    var home = document.getElementById("home"), btn = document.getElementById("goBtn");
    var panel = document.getElementById("working"), msg = document.getElementById("wkMsg"), time = document.getElementById("wkTime");
    var started = false;
    form.addEventListener("submit", function () {
      if (started) return;           /* native validation already passed */
      started = true;
      home.classList.add("loading");
      panel.hidden = false;
      btn.disabled = true;           /* after the submit event, so the form still posts */
      btn.textContent = "Checking…";
      window.scrollTo({ top: 0, behavior: "smooth" });
      var t0 = Date.now();
      var iv = setInterval(function () {
        var s = Math.floor((Date.now() - t0) / 1000);
        time.textContent = Math.floor(s / 60) + ":" + ("0" + (s % 60)).slice(-2);
        if (s === 45) msg.textContent = "Still working — listings with many photos take a bit longer.";
        if (s > 300) clearInterval(iv);
      }, 1000);
    });
  }

  /* analyses: live filter + delete confirmation */
  var filter = document.getElementById("filter");
  if (filter) {
    var rows = document.querySelectorAll("#rows .hrow"), count = document.getElementById("count");
    filter.addEventListener("input", function () {
      var q = filter.value.trim().toLowerCase(), n = 0;
      for (var k = 0; k < rows.length; k++) {
        var hit = !q || rows[k].dataset.s.indexOf(q) !== -1;
        rows[k].hidden = !hit; if (hit) n++;
      }
      if (count) count.textContent = n + (n === 1 ? " analysis" : " analyses");
    });
  }
  var dels = document.querySelectorAll("form[data-confirm]");
  for (var d = 0; d < dels.length; d++) {
    dels[d].addEventListener("submit", function (e) {
      if (!confirm(this.dataset.confirm)) e.preventDefault();
    });
  }

  /* secret fields: cleared right after their form submits (the browser has
     already serialized the body) and whenever the page is restored from the
     back/forward cache, so a key never lingers in a form on a shared machine */
  var secrets = document.querySelectorAll("input[data-secret]");
  function wipeSecrets() { for (var w = 0; w < secrets.length; w++) secrets[w].value = ""; }
  for (var s = 0; s < secrets.length; s++) {
    secrets[s].form && secrets[s].form.addEventListener("submit", function () { setTimeout(wipeSecrets, 0); });
  }
  window.addEventListener("pageshow", function (e) { if (e.persisted) wipeSecrets(); });
  window.addEventListener("pagehide", wipeSecrets);

  /* [data-toggle="id"] buttons show/hide a panel */
  var tg = document.querySelectorAll("[data-toggle]");
  for (var t = 0; t < tg.length; t++) {
    tg[t].addEventListener("click", function () {
      var el = document.getElementById(this.dataset.toggle);
      if (!el) return;
      el.hidden = !el.hidden;
      this.setAttribute("aria-expanded", el.hidden ? "false" : "true");
      if (!el.hidden) { var f = el.querySelector("input"); if (f) f.focus(); }
    });
  }

  /* any form with data-busy: disable its submit button and relabel it while the request runs */
  var busy = document.querySelectorAll("form[data-busy]");
  for (var b = 0; b < busy.length; b++) {
    busy[b].addEventListener("submit", function () {
      var btn = this.querySelector("button[type=submit]");
      if (btn) { setTimeout(function () { btn.disabled = true; }, 0); btn.textContent = this.dataset.busy; }
    });
  }

  /* API keys: confirm "free-tier only" before a key leaves the browser.
     Forms with data-key-ack open the dialog when a secret field is filled; the
     server also refuses a key without key_ack=1, so this is a second gate, not the only one. */
  var ackDialog = document.getElementById("keyAck");
  var ackForms = document.querySelectorAll("form[data-key-ack]");
  if (ackDialog && ackForms.length) {
    var ackBox = document.getElementById("keyAckBox"), ackGo = document.getElementById("keyAckGo"), pendingForm = null;
    ackBox.addEventListener("change", function () { ackGo.disabled = !ackBox.checked; });
    ackDialog.querySelector("[data-ack-cancel]").addEventListener("click", function () { ackDialog.close(); pendingForm = null; });
    ackGo.addEventListener("click", function () {
      if (!ackBox.checked || !pendingForm) return;
      var f = pendingForm; pendingForm = null;
      var field = f.querySelector("[data-key-ack-field]"); if (field) field.value = "1";
      ackDialog.close();
      f.requestSubmit ? f.requestSubmit() : f.submit();
    });
    for (var a = 0; a < ackForms.length; a++) {
      ackForms[a].addEventListener("submit", function (e) {
        var sub = e.submitter; if (sub && sub.value === "remove") return;          /* removing a key needs no warning */
        var field = this.querySelector("[data-key-ack-field]");
        if (field && field.value === "1") return;                                    /* already confirmed */
        var filled = false, secretsHere = this.querySelectorAll("input[data-secret]");
        for (var q = 0; q < secretsHere.length; q++) if (secretsHere[q].value.trim()) filled = true;
        if (!filled) return;                                                         /* no key typed: nothing to confirm */
        e.preventDefault();
        pendingForm = this; ackBox.checked = false; ackGo.disabled = true;
        if (typeof ackDialog.showModal === "function") ackDialog.showModal(); else ackDialog.setAttribute("open", "");
      });
    }
  }

  /* copy-to-clipboard blocks */
  var copies = document.querySelectorAll("[data-copy]");
  for (var c = 0; c < copies.length; c++) {
    copies[c].addEventListener("click", function () {
      var btn = this, text = btn.parentNode.querySelector(".copytext").textContent;
      var done = function () { var old = btn.textContent; btn.textContent = "Copied"; setTimeout(function () { btn.textContent = old; }, 1500); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, done);
      else { var ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select(); try { document.execCommand("copy"); } catch (e) {} document.body.removeChild(ta); done(); }
    });
  }

  /* report: the assistant panel. One POST per question; the server meters and scopes. */
  var chatBox = document.getElementById("chat");
  if (chatBox) {
    var log = document.getElementById("chatLog"), cform = document.getElementById("chatForm"), q = document.getElementById("chatQ");
    var go = document.getElementById("chatGo"), left = document.getElementById("chatLeft"), note = document.getElementById("chatNote");
    var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
    var own = chatBox.dataset.own === "1";
    function bubble(text, cls) { var d = document.createElement("div"); d.className = "bubble " + cls; d.textContent = text; log.appendChild(d); log.scrollTop = log.scrollHeight; return d; }
    function setLeft(n) { if (own) return; left.textContent = n + " of " + chatBox.dataset.cap + " free questions left today"; if (n <= 0) { q.disabled = true; go.disabled = true; note.hidden = false; } }
    function send() {
      var text = q.value.trim(); if (!text || go.disabled) return;
      var hint = log.querySelector(".hint"); if (hint) hint.remove();
      bubble(text, "me"); q.value = ""; go.disabled = true; q.disabled = true;
      var wait = bubble("…", "bot wait");
      fetch(chatBox.dataset.url, { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify({ q: text }) })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, status: r.status, j: j }; }); })
        .then(function (res) {
          wait.remove();
          if (res.ok) { bubble(res.j.answer, "bot" + (res.j.in_scope ? "" : " off")); if (typeof res.j.remaining === "number") setLeft(res.j.remaining); }
          else { bubble(res.j.error || "Something went wrong.", "bot off"); if (res.j.capped) setLeft(0); }
        })
        .catch(function () { wait.remove(); bubble("Couldn't reach BEAT. Check your connection and try again.", "bot off"); })
        .then(function () { if (!(q.disabled && note && !note.hidden)) { go.disabled = false; q.disabled = false; q.focus(); } });
    }
    cform.addEventListener("submit", function (e) { e.preventDefault(); send(); });
    q.addEventListener("keydown", function (e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
    log.scrollTop = log.scrollHeight;
  }

  /* price pages: the model finder. The server renders every chip; this only filters them and shows the panel. */
  var finder = document.getElementById("finder");
  if (finder) {
    var fq = document.getElementById("finderQ"), fpanel = document.getElementById("finderPanel"), fgo = document.getElementById("finderGo");
    var fchips = [].slice.call(fpanel.querySelectorAll(".mchip")), fempty = fpanel.querySelector(".finder-empty");
    var fnorm = function (s) { return (s || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim(); };
    var fopen = function (on) { fpanel.hidden = !on; fq.setAttribute("aria-expanded", on ? "true" : "false"); };
    var ffilter = function () {
      var words = fnorm(fq.value).split(" ").filter(Boolean), shown = 0;
      fchips.forEach(function (c) {
        var ok = words.every(function (w) { return c.dataset.text.indexOf(w) !== -1; });
        c.hidden = !ok; if (ok) shown++;
      });
      if (fempty) fempty.hidden = !(fchips.length && shown === 0);
    };
    fq.addEventListener("focus", function () { ffilter(); fopen(true); });
    fq.addEventListener("input", function () { ffilter(); fopen(true); });
    fgo.addEventListener("click", function () { fq.focus(); ffilter(); fopen(true); });
    fq.addEventListener("keydown", function (e) {
      if (e.key === "Escape") fopen(false);
      if (e.key === "Enter") {
        e.preventDefault();
        var first = fchips.filter(function (c) { return !c.hidden; })[0];
        if (first) window.location = first.href;
      }
    });
    document.addEventListener("click", function (e) { if (!finder.contains(e.target)) fopen(false); });
  }

  /* report: tabs with hash deep-links (/report/5#price) */
  var bar = document.querySelector("#result .tabs");
  if (bar) {
    var btns = [].slice.call(bar.querySelectorAll("button[data-t]")), panels = {};
    var tps = document.querySelectorAll("#result .tp[data-t]");
    for (var p = 0; p < tps.length; p++) panels[tps[p].dataset.t] = tps[p];
    var first = btns.length ? btns[0].dataset.t : "";
    function show(key, push) {
      if (!panels[key]) key = first;
      btns.forEach(function (b) {
        var on = b.dataset.t === key;
        b.classList.toggle("on", on);
        b.setAttribute("aria-selected", on ? "true" : "false");
      });
      for (var k in panels) panels[k].hidden = k !== key;
      if (push && history.replaceState) history.replaceState(null, "", "#" + key);
    }
    btns.forEach(function (b) { b.addEventListener("click", function () { show(b.dataset.t, true); }); });
    show((location.hash || "").replace("#", ""), false);
  }
})();
